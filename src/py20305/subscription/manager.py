"""SubscriptionManager for IEEE 2030.5 subscription lifecycle.

Manages creating, cancelling, and tracking subscriptions via the Sep2Client.
Stores notifications in a bounded deque to prevent unbounded memory growth.
Supports automatic renewal of subscriptions before server-side expiry.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from py20305.client.errors import Sep2ProtocolError
from py20305.models.sep.sep import Subscription

if TYPE_CHECKING:
    from py20305.client.http import Sep2Client

logger = logging.getLogger(__name__)

# HTTP statuses on a subscription renewal that signal "the server has
# forgotten this subscription" (typically because the server restarted
# and lost its in-memory subscription store, or the subscription was
# deleted out-of-band via the mgmt API). On these, falling back to a
# full rediscovery is correct: the next subscribe cycle will recreate
# the subscription against the server's current resource layout.
_SUBSCRIPTION_LOST_STATUSES: frozenset[int] = frozenset({401, 404})


@dataclass
class SubscriptionState:
    """Tracks a single active subscription."""

    subscription_uri: str
    subscribed_resource: str
    notification_uri: str
    resource_type: str
    status: str = "active"
    created_at: float = field(default_factory=time.time)
    is_list_subscription: bool = False
    unmanaged: bool = False


@dataclass
class StoredNotification:
    """A received notification record."""

    subscribed_resource: str
    status: int
    subscription_uri: str
    created_at: float
    new_resource_uri: str | None = None


@dataclass
class ReconcileResult:
    """Summary of subscription reconciliation during rediscovery."""

    kept: int = 0
    cancelled: int = 0
    created: int = 0
    renewed: int = 0
    cancel_errors: int = 0
    create_errors: int = 0


class SubscriptionManager:
    """Create, cancel, and track IEEE 2030.5 subscriptions.

    Uses Sep2Client for HTTP operations and maintains in-memory state
    of active subscriptions and received notifications.
    """

    def __init__(
        self,
        client: Sep2Client,
        notification_uri: str,
        *,
        max_notifications: int = 1000,
        server_2018_compat: bool = False,
        on_subscription_lost: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._client = client
        self._notification_uri = notification_uri
        self._subscriptions: dict[str, SubscriptionState] = {}
        self._notifications: deque[StoredNotification] = deque(maxlen=max_notifications)
        self._dedup_cache: dict[str, float] = {}
        # Resources the server has cancelled (status=1) -- auto-subscribe skips
        # these so we honour the cancellation (poll-only) instead of immediately
        # re-subscribing. Persists across rediscovery; cleared only on a genuine
        # reconnect.
        self._resubscribe_suppressed: set[str] = set()
        self._server_2018_compat = server_2018_compat
        # Invoked (fire-and-forget) when a renewal returns 401/404 -- the
        # server has forgotten our subscriptions and a full rediscovery is
        # needed to re-establish them. The caller (typically csip_client)
        # wires this to its trigger_rediscovery; the rediscovery primitive
        # has its own concurrency lock so duplicate firings collapse.
        self._on_subscription_lost = on_subscription_lost

    def _find_active_for_resource(self, subscribed_resource: str) -> SubscriptionState | None:
        """Find an existing active subscription for a resource href."""
        for state in self._subscriptions.values():
            if state.subscribed_resource == subscribed_resource and state.status == "active":
                return state
        return None

    def suppress_resubscribe(self, subscribed_resource: str) -> None:
        """Mark a resource as server-cancelled so auto-subscribe won't re-POST it.

        Set on a ``status=1`` (canceled, no additional info) termination: we
        honour the cancellation (poll the resource instead) rather than fighting
        the server by immediately re-subscribing. Persists across rediscovery;
        cleared by :meth:`clear_resubscribe_suppression` only on a genuine
        reconnect.
        """
        self._resubscribe_suppressed.add(subscribed_resource)

    def resubscribe_suppressed(self, subscribed_resource: str) -> bool:
        """True if auto-subscribe should skip this server-cancelled resource."""
        return subscribed_resource in self._resubscribe_suppressed

    def clear_resubscribe_suppression(self) -> None:
        """Clear all resubscribe suppression. Called only on a genuine reconnect
        (upstream restart), not on every rediscovery, so an honoured status=1
        cancellation survives the rediscovery storm."""
        self._resubscribe_suppressed.clear()

    async def subscribe(
        self,
        sub_list_href: str,
        subscribed_resource: str,
        resource_type: str,
        *,
        encoding: int = 0,
        level: str = "+S1",
        limit: int = 1,
    ) -> SubscriptionState | None:
        """Create a subscription by POSTing to the server's SubscriptionList.

        Args:
            sub_list_href: Server path to SubscriptionList resource.
            subscribed_resource: Full resource path to subscribe to.
            resource_type: Human-readable type (e.g. "FSAList").
            encoding: 0=XML, 1=EXI.
            level: Schema level string.
            limit: Max list items in notification.

        Returns:
            SubscriptionState on success, None on failure.
        """
        # Dedup: return existing active subscription for the same resource
        existing = self._find_active_for_resource(subscribed_resource)
        if existing is not None:
            logger.debug(
                "Already subscribed to %s via %s, skipping POST",
                subscribed_resource,
                existing.subscription_uri,
            )
            return existing

        sub = Subscription(
            subscribed_resource=subscribed_resource,
            encoding=encoding,
            level=level,
            limit=limit,
            notification_uri=self._notification_uri,
        )

        try:
            location = await self._client.post(sub_list_href, sub)
        except Sep2ProtocolError as exc:
            from py20305.diagnostics import report

            report(
                "warnings",
                (
                    f"Server rejected subscription to {subscribed_resource} at "
                    f"{sub_list_href}: HTTP {exc.status_code}"
                ),
                source="subscription",
                dedup_key=f"subscribe:{subscribed_resource}",
                details={
                    "subscribed_resource": subscribed_resource,
                    "sub_list_href": sub_list_href,
                    "status_code": exc.status_code,
                },
            )
            return None
        except Exception as exc:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Failed to subscribe to {subscribed_resource} at {sub_list_href}: {exc}",
                source="subscription",
                dedup_key=f"subscribe:{subscribed_resource}",
                details={
                    "subscribed_resource": subscribed_resource,
                    "sub_list_href": sub_list_href,
                    "error": str(exc),
                },
                exc_info=True,
            )
            return None

        if not location:
            if self._server_2018_compat:
                location = f"_synthetic:{subscribed_resource}"
                logger.info(
                    "No Location header (2018 compat), using synthetic URI for %s",
                    subscribed_resource,
                )
                state = SubscriptionState(
                    subscription_uri=location,
                    subscribed_resource=subscribed_resource,
                    notification_uri=self._notification_uri,
                    resource_type=resource_type,
                    is_list_subscription=resource_type.endswith("List"),
                    unmanaged=True,
                )
                self._subscriptions[location] = state
                return state

            from py20305.diagnostics import report

            report(
                "warnings",
                f"No Location header for subscription to {subscribed_resource}",
                source="subscription",
                dedup_key=f"sub_no_location:{subscribed_resource}",
                details={"subscribed_resource": subscribed_resource},
            )
            return None

        state = SubscriptionState(
            subscription_uri=location,
            subscribed_resource=subscribed_resource,
            notification_uri=self._notification_uri,
            resource_type=resource_type,
            is_list_subscription=resource_type.endswith("List"),
        )
        self._subscriptions[location] = state
        logger.info("Subscribed to %s (%s) -> %s", subscribed_resource, resource_type, location)
        return state

    async def cancel(self, subscription_uri: str) -> bool:
        """Cancel a subscription by DELETEing it on the server.

        Returns True if the DELETE succeeded or the subscription was unknown.
        Always removes local state to prevent stale entries blocking re-subscription.
        Unmanaged subscriptions (2018 compat) skip the DELETE.
        """
        state = self._subscriptions.get(subscription_uri)
        if state is not None and state.unmanaged:
            self._subscriptions.pop(subscription_uri, None)
            logger.info("Removed unmanaged subscription %s", subscription_uri)
            return True

        try:
            await self._client.delete(subscription_uri)
        except Exception:
            logger.warning("Failed to cancel subscription %s", subscription_uri, exc_info=True)
            self._subscriptions.pop(subscription_uri, None)
            return False

        self._subscriptions.pop(subscription_uri, None)
        logger.info("Cancelled subscription %s", subscription_uri)
        return True

    async def _cancel_one(self, subscription_uri: str) -> bool:
        """Cancel a single subscription (DELETE only, no dict mutation).

        Unmanaged subscriptions skip the DELETE.
        """
        state = self._subscriptions.get(subscription_uri)
        if state is not None and state.unmanaged:
            logger.info("Skipping DELETE for unmanaged subscription %s", subscription_uri)
            return True
        try:
            await self._client.delete(subscription_uri)
            logger.info("Cancelled subscription %s", subscription_uri)
            return True
        except Exception:
            logger.warning("Failed to cancel subscription %s", subscription_uri, exc_info=True)
            return False

    async def cancel_all(self) -> None:
        """Cancel all active subscriptions in parallel, tolerating individual errors."""
        uris = list(self._subscriptions.keys())
        if not uris:
            return
        results = await asyncio.gather(
            *(self._cancel_one(uri) for uri in uris),
            return_exceptions=True,
        )
        for uri, result in zip(uris, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("Error cancelling %s: %s", uri, result)
        # Always clear all local state -- stale entries block re-subscription
        self._subscriptions.clear()

    async def reconcile(
        self,
        sub_list_href: str,
        desired: set[tuple[str, str]],
        *,
        renew_kept: bool = True,
    ) -> ReconcileResult:
        """Diff-based subscription reconciliation.

        Compares active subscriptions against *desired* (a set of
        ``(subscribed_resource, resource_type)`` tuples) and issues the
        necessary CANCEL / CREATE calls.  When ``renew_kept`` is True,
        subscriptions present in both sets are renewed (re-POSTed) to verify
        they still exist on the server; if a renewal fails (e.g. the server was
        reset), the subscription is cancelled locally and recreated.

        Args:
            sub_list_href: Server SubscriptionList path for new POSTs.
            desired: Target subscription set after rediscovery.
            renew_kept: Re-POST kept subscriptions to verify server-side
                validity. Pass False from the rediscovery path: re-POSTing on
                every rediscovery provokes some servers to echo a current-state
                notification, which the client classifies as a structural
                change and rediscovers again -- an infinite loop. Subscription
                lifetime is the periodic renewal task's responsibility, not
                rediscovery's.

        Returns:
            ReconcileResult with counts for each action taken.
        """
        result = ReconcileResult()

        # Build lookup of active subs keyed by subscribed_resource
        old_by_resource: dict[str, SubscriptionState] = {}
        for state in list(self._subscriptions.values()):
            if state.status != "active":
                # Clean up stale cancelled entries
                self._subscriptions.pop(state.subscription_uri, None)
                continue
            old_by_resource[state.subscribed_resource] = state

        desired_map: dict[str, str] = dict(desired)

        old_resources = set(old_by_resource)
        desired_resources = set(desired_map)

        # Partition into keep / cancel / create, accounting for type changes
        keep_resources: set[str] = set()
        cancel_resources: set[str] = set()
        create_resources: set[str] = set()

        for resource in old_resources & desired_resources:
            if old_by_resource[resource].resource_type == desired_map[resource]:
                keep_resources.add(resource)
            else:
                # Type changed: cancel old, create new
                cancel_resources.add(resource)
                create_resources.add(resource)

        cancel_resources |= old_resources - desired_resources
        create_resources |= desired_resources - old_resources

        # CANCEL stale subscriptions (parallel)
        cancel_uris = [old_by_resource[r].subscription_uri for r in cancel_resources]
        if cancel_uris:
            cancel_outcomes = await asyncio.gather(
                *(self._cancel_one(uri) for uri in cancel_uris),
                return_exceptions=True,
            )
            for uri, ok in zip(cancel_uris, cancel_outcomes, strict=True):
                self._subscriptions.pop(uri, None)
                result.cancelled += 1
                if isinstance(ok, BaseException) or not ok:
                    result.cancel_errors += 1

        # KEEP stable subscriptions. When renew_kept, re-POST in parallel to
        # verify server-side validity (recreating any the server dropped);
        # otherwise keep them as-is without touching the wire.
        keep_list = [(r, old_by_resource[r]) for r in keep_resources]
        if keep_list and renew_kept:
            renew_outcomes = await asyncio.gather(
                *(self.renew(state.subscription_uri) for _, state in keep_list),
                return_exceptions=True,
            )
            for (resource, state), ok in zip(keep_list, renew_outcomes, strict=True):
                if isinstance(ok, BaseException) or not ok:
                    # Renewal failed -- subscription is stale on server.
                    # Cancel local state and fall through to CREATE.
                    self._subscriptions.pop(state.subscription_uri, None)
                    create_resources.add(resource)
                    result.cancelled += 1
                else:
                    result.kept += 1
                    result.renewed += 1
        elif keep_list:
            result.kept += len(keep_list)

        # CREATE new subscriptions (parallel)
        create_list = [(r, desired_map[r]) for r in create_resources]
        if create_list:
            create_outcomes = await asyncio.gather(
                *(self.subscribe(sub_list_href, r, rtype) for r, rtype in create_list),
                return_exceptions=True,
            )
            for _, sub_state in zip(create_list, create_outcomes, strict=True):
                if isinstance(sub_state, BaseException) or sub_state is None:
                    result.create_errors += 1
                else:
                    result.created += 1

        logger.info(
            "Reconciled subscriptions: kept=%d cancelled=%d created=%d "
            "renewed=%d cancel_errors=%d create_errors=%d",
            result.kept,
            result.cancelled,
            result.created,
            result.renewed,
            result.cancel_errors,
            result.create_errors,
        )
        return result

    async def reconcile_with_server(
        self, sub_list_href: str, *, reestablish_missing: bool = True
    ) -> int:
        """Reconcile local subscriptions against the server's SubscriptionList.

        GETs the server's SubscriptionList and:
        - **adopts the server's current URI** for any resource whose local URI has
          drifted (server dropped+recreated or renumbered the subscription) -- a
          local re-key, no wire writes; and
        - when ``reestablish_missing`` is True, **re-POSTs** any subscription that
          is active locally but absent from the server's list (e.g. dropped after
          a notification-delivery failure). Returns the number re-established.

        Pass ``reestablish_missing=False`` from the rediscovery path: re-POSTing
        during rediscovery can make some servers echo a current-state
        notification, which the client classifies as a structural change and
        rediscovers again -- an infinite loop. With
        ``reestablish_missing=False`` this method makes no POSTs: it adopts a
        drifted URI (local re-key) and, for a subscription the server no longer
        has at all, *drops* the stale local entry rather than re-POSTing it.
        Re-subscription of the (still-desired) resource is handled by
        ``_auto_subscribe`` after rediscovery, as a normal new subscription.

        One-way: server-side subscriptions we don't track are left untouched. Per
        IEEE 2030.5 this is meant to run at the SubscriptionList ``pollRate``
        (default 900s); the SubscriptionList isn't itself subscribed, so §8.9.3.4
        rule (r) doesn't prohibit polling it.
        """
        from py20305.diagnostics import report
        from py20305.models.sep.sep import SubscriptionList

        try:
            pages = await self._client.get_list(sub_list_href, SubscriptionList)
        except Exception as exc:  # a poll-time GET must never throw out of the loop
            # Any GET failure (HTTP error, transport/OSError, malformed payload)
            # is a logged no-op; the next reconcile tick retries.
            status = getattr(exc, "status_code", None)
            report(
                "warnings",
                f"SubscriptionList reconcile GET failed at {sub_list_href}: {exc}",
                source="subscription",
                dedup_key=f"sub_reconcile_get:{sub_list_href}",
                details={"sub_list_href": sub_list_href, "status": status},
            )
            return 0

        # Track resource *presence* separately from the server's URI. A server
        # entry may carry a subscribedResource but an empty/missing href
        # (Resource.href is optional), and resource presence -- not href -- is what
        # gates re-establishment (matching the prior semantics). The href only
        # gates URI adoption: if the server dropped and recreated (or renumbered)
        # the subscription for a resource, it's still present but under a new URI,
        # and our stale local URI would 404 on a later cancel/renew.
        server_resources: set[str] = set()
        server_uri_by_resource: dict[str, str] = {}
        for page in pages:
            for sub in page.subscription or []:
                if not sub.subscribed_resource:
                    continue
                server_resources.add(sub.subscribed_resource)
                if sub.href:
                    server_uri_by_resource[sub.subscribed_resource] = sub.href

        re_established = 0
        adopted = 0
        dropped = 0
        for state in list(self.active_subscriptions):
            if state.unmanaged:
                continue  # operator/externally-managed -- we don't own its lifecycle
            if state.subscribed_resource in server_resources:
                # Resource is subscribed on the server. Adopt the server's URI only
                # when it gave us a non-empty href that differs from our local one,
                # so what we report/cancel/renew matches what the server holds.
                server_uri = server_uri_by_resource.get(state.subscribed_resource)
                if server_uri is not None and server_uri != state.subscription_uri:
                    self._subscriptions.pop(state.subscription_uri, None)
                    state.subscription_uri = server_uri
                    self._subscriptions[server_uri] = state
                    adopted += 1
                continue
            if not reestablish_missing:
                # Rediscovery path: don't re-POST (echo-loop risk). If the server
                # returned a *populated* SubscriptionList that omits this resource,
                # it genuinely dropped the subscription -- drop our stale local
                # entry so we don't keep reporting (or later cancel/renew) a URI
                # the server 404s on. The resource is still desired, so
                # _auto_subscribe (run at the end of rediscovery) re-subscribes it
                # with a fresh URI as a normal new subscription. We require a
                # non-empty list so an empty/unhelpful response can't wipe every
                # local subscription.
                if server_resources:
                    logger.info(
                        "Subscription for %s absent from a populated server "
                        "SubscriptionList; dropping stale local entry "
                        "(rediscovery re-subscribes)",
                        state.subscribed_resource,
                    )
                    self._subscriptions.pop(state.subscription_uri, None)
                    dropped += 1
                continue
            logger.info(
                "Subscription for %s missing from server SubscriptionList; re-establishing",
                state.subscribed_resource,
            )
            # Drop the stale entry so subscribe() actually re-POSTs (it dedups on
            # active local state). Restore it if the re-POST fails transiently, so
            # the next reconcile retries instead of silently forgetting the sub
            # (which, if it were the last one, would stop the reconcile poll).
            self._subscriptions.pop(state.subscription_uri, None)
            if await self.subscribe(sub_list_href, state.subscribed_resource, state.resource_type):
                re_established += 1
            else:
                self._subscriptions[state.subscription_uri] = state
        if re_established:
            logger.info("Reconcile re-established %d dropped subscription(s)", re_established)
        if adopted:
            logger.info("Reconcile adopted %d server-side subscription URI(s)", adopted)
        if dropped:
            logger.info("Reconcile dropped %d stale subscription(s) absent from server", dropped)
        return re_established

    def record_notification(self, notification: StoredNotification) -> None:
        """Append a notification to the bounded store."""
        self._notifications.append(notification)

    def mark_cancelled(self, subscription_uri: str) -> None:
        """Mark a subscription as cancelled (server-initiated)."""
        state = self._subscriptions.get(subscription_uri)
        if state:
            state.status = "cancelled"

    def remove_notifications_for(self, subscribed_resource: str) -> None:
        """Remove all stored notifications for a given resource."""
        self._notifications = deque(
            (n for n in self._notifications if n.subscribed_resource != subscribed_resource),
            maxlen=self._notifications.maxlen,
        )

    @property
    def active_subscriptions(self) -> list[SubscriptionState]:
        """Return subscriptions with status 'active'."""
        return [s for s in self._subscriptions.values() if s.status == "active"]

    @property
    def notifications(self) -> list[StoredNotification]:
        """Return all stored notifications (oldest first)."""
        return list(self._notifications)

    def subscribed_resource_types(self) -> set[str]:
        """Return the set of resource_type values for active subscriptions."""
        return {s.resource_type for s in self._subscriptions.values() if s.status == "active"}

    def to_checkpoint(self) -> dict[str, Any]:
        """Serialize subscription state for persistence."""
        return {
            "subscriptions": [
                {
                    "subscription_uri": s.subscription_uri,
                    "subscribed_resource": s.subscribed_resource,
                    "notification_uri": s.notification_uri,
                    "resource_type": s.resource_type,
                    "status": s.status,
                    "created_at": s.created_at,
                    "unmanaged": s.unmanaged,
                }
                for s in self._subscriptions.values()
            ],
        }

    def restore_from_checkpoint(self, data: dict[str, Any]) -> None:
        """Restore subscription state from a checkpoint."""
        self._subscriptions.clear()
        for entry in data.get("subscriptions", []):
            state = SubscriptionState(
                subscription_uri=entry["subscription_uri"],
                subscribed_resource=entry["subscribed_resource"],
                notification_uri=entry["notification_uri"],
                resource_type=entry["resource_type"],
                status=entry.get("status", "active"),
                created_at=entry.get("created_at", 0.0),
                is_list_subscription=entry.get("is_list_subscription", False),
                unmanaged=entry.get("unmanaged", False),
            )
            self._subscriptions[state.subscription_uri] = state

    async def validate_restored_subscriptions(self) -> tuple[int, int]:
        """Validate checkpoint-restored subscriptions by attempting renewal.

        Returns (valid_count, removed_count).  Subscriptions that fail
        renewal are removed from the active set.
        """
        valid = 0
        removed = 0
        for uri in list(self._subscriptions.keys()):
            state = self._subscriptions.get(uri)
            if state is None or state.status != "active":
                continue
            if state.unmanaged:
                valid += 1
                continue
            if await self.renew(uri):
                valid += 1
            else:
                self._subscriptions.pop(uri, None)
                removed += 1
                logger.info("Removed stale subscription %s (%s)", uri, state.resource_type)
        return valid, removed

    # ------------------------------------------------------------------
    # WP4b: Subscription renewal
    # ------------------------------------------------------------------

    async def renew(
        self,
        subscription_uri: str,
        *,
        encoding: int | None = None,
        level: str | None = None,
        limit: int | None = None,
    ) -> bool:
        """Renew a single subscription by re-POSTing to the server.

        Optional parameter overrides allow changing encoding, level, or limit
        during renewal (WP4c).

        Returns True on success, False on failure.
        """
        state = self._subscriptions.get(subscription_uri)
        if state is None or state.status != "active":
            return False

        if state.unmanaged:
            state.created_at = time.time()
            logger.info("Renewed unmanaged subscription %s (timestamp only)", subscription_uri)
            return True

        sub = Subscription(
            subscribed_resource=state.subscribed_resource,
            encoding=encoding if encoding is not None else 0,
            level=level if level is not None else "+S1",
            limit=limit if limit is not None else 1,
            notification_uri=state.notification_uri,
        )

        # POST to the SubscriptionList (parent), not the individual subscription.
        # The server matches by notificationURI to treat this as a renewal.
        sub_list_href = subscription_uri.rsplit("/", 1)[0]

        try:
            location = await self._client.post(sub_list_href, sub)
        except Sep2ProtocolError as exc:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Server rejected renewal of {subscription_uri}: HTTP {exc.status_code}",
                source="subscription",
                dedup_key=f"sub_renewal:{subscription_uri}",
                details={
                    "subscription_uri": subscription_uri,
                    "status_code": exc.status_code,
                },
            )
            if exc.status_code in _SUBSCRIPTION_LOST_STATUSES and self._on_subscription_lost:
                # Server forgot about this subscription (typical after a
                # server restart drops the in-memory subscription store).
                # Trigger rediscovery so a fresh subscribe cycle runs
                # against the server's current state; the rediscovery
                # primitive coalesces concurrent calls, so multiple
                # renewals firing this at once is safe.
                logger.info(
                    "Renewal of %s returned HTTP %d -- server appears to have "
                    "forgotten our subscription; triggering rediscovery",
                    subscription_uri,
                    exc.status_code,
                )
                callback = self._on_subscription_lost

                async def _fire() -> None:
                    await callback()

                asyncio.create_task(  # noqa: RUF006 -- fire-and-forget by design
                    _fire(),
                    name="subscription-lost-rediscovery",
                )
            return False
        except Exception as exc:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Failed to renew subscription {subscription_uri}: {exc}",
                source="subscription",
                dedup_key=f"sub_renewal:{subscription_uri}",
                details={"subscription_uri": subscription_uri, "error": str(exc)},
                exc_info=True,
            )
            return False

        if location and location != subscription_uri:
            # Server returned a new URI on renewal
            self._subscriptions.pop(subscription_uri, None)
            state.subscription_uri = location
            self._subscriptions[location] = state

        state.created_at = time.time()
        logger.info("Renewed subscription %s (%s)", subscription_uri, state.resource_type)
        return True

    async def renew_all(self) -> int:
        """Renew all active subscriptions. Returns count of successful renewals."""
        count = 0
        for uri in list(self._subscriptions.keys()):
            state = self._subscriptions.get(uri)
            if state and state.status == "active" and await self.renew(uri):
                count += 1
        return count

    async def start_renewal_task(
        self,
        shutdown: asyncio.Event,
        interval_seconds: int = 86400,
    ) -> None:
        """Run subscription renewal loop until shutdown is signaled.

        Renews subscriptions that are older than ``interval_seconds``
        (default 24h). IEEE 2030.5 server-side subscriptions expire after 36h.
        """
        while not shutdown.is_set():
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=interval_seconds)
                break  # shutdown signaled
            except TimeoutError:
                pass  # interval elapsed, time to renew

            # Renew stale subscriptions
            now = time.time()
            for uri in list(self._subscriptions.keys()):
                state = self._subscriptions.get(uri)
                if state and state.status == "active":
                    age = now - state.created_at
                    if age >= interval_seconds:
                        await self.renew(uri)

    # ------------------------------------------------------------------
    # WP4d: Non-list vs list notification semantics
    # ------------------------------------------------------------------

    def should_process_notification(self, subscription_uri: str, resource_href: str) -> bool:
        """Check if a notification should be processed based on subscription type.

        Non-list resource subscriptions should not fire on ListLink attribute changes.
        """
        state = self._subscriptions.get(subscription_uri)
        if state is None:
            return True  # Unknown subscription, process anyway
        return state.is_list_subscription or resource_href == state.subscribed_resource

    def resource_type_for(self, subscription_uri: str) -> str | None:
        """Return the recorded resource_type for a subscription, or None.

        The type was assigned at subscribe time from the discovered topology
        (which link the client subscribed to), so it's independent of how the
        server names its URLs -- unlike inferring the type from the notification
        path. None when the subscription isn't tracked locally (callers fall back
        to a path heuristic).
        """
        state = self._subscriptions.get(subscription_uri)
        return state.resource_type if state is not None else None

    # ------------------------------------------------------------------
    # WP4e: Duplicate notification deduplication
    # ------------------------------------------------------------------

    _DEDUP_WINDOW: float = 5.0

    def is_duplicate_notification(self, resource_href: str) -> bool:
        """Check if a notification for this resource is a duplicate (within 5s window)."""
        now = time.time()
        last = self._dedup_cache.get(resource_href)
        if last is not None and now - last < self._DEDUP_WINDOW:
            return True
        self._dedup_cache[resource_href] = now
        return False

    def record_notification_ancestry(self, resource_href: str) -> None:
        """Record program-scope parent paths in dedup cache after a targeted fetch.

        When a targeted fetch for /edev/1/derp/3/derc succeeds, record ancestor
        paths up to and including /derp (/edev/1/derp/3, /edev/1/derp) so that
        the server's same-batch DERProgram and DERProgramList notifications
        are suppressed within the dedup window.

        Walk stops at /derp. FSAList (/edev/.../fsa, /edev/.../fsa/N) and
        EndDeviceList (/edev, /edev/N) ancestors represent independent structural
        changes that fire alongside /derc bursts during mid-test mutations
        (e.g. conformance test BASIC-003 remove_derp + add_fsa). Suppressing them would
        swallow legitimate full-rediscovery triggers.
        """
        now = time.time()
        path = resource_href.rstrip("/")
        parts = path.strip("/").split("/")
        for i in range(len(parts) - 1, 0, -1):
            parent = "/" + "/".join(parts[:i])
            self._dedup_cache[parent] = now
            if parts[i - 1] == "derp":
                break
