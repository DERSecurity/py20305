"""Async poll scheduler with mutual exclusion and graceful shutdown."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from py20305.client.errors import Sep2ConnectionError, Sep2ProtocolError

logger = logging.getLogger(__name__)

_SKIP_LOG_THROTTLE = 30.0  # seconds between "skip" log messages per key

# Heartbeat baseline poll. Even when a subscription covers
# a resource and the regular poll is suppressed, we still fire the
# callback as a safety net for missed notifications:
#   * server dropped or never sent the notification (network blip,
#     overloaded listener, mTLS rejection)
#   * the server restarted and lost its subscription store
#
#   * server bug causes silent notification loss
#
# Without this, missed-notification recovery is the 24h renewal cycle
# or an operator-triggered rediscovery -- both far too slow for the
# resources that drive DER behavior (DERControl, DDERC).
#
# Spec alignment: IEEE 2030.5 §8.9.3.4 gives us three relevant SHOULDs.
#   (r) p.84 "Clients SHOULD NOT poll resources for which they have an
#       active Subscription."
#   (g) p.83 "Following recovery from a perceived loss of connectivity,
#       clients SHOULD poll their resources of interest (including
#       those to which they are subscribed) in case those resources
#       changed during the loss of connectivity."
#   (f) p.83 "Clients SHOULD check the status of their Subscriptions
#       after perceived loss of connectivity."
#
# Rule (g) is the spec-explicit basis for the heartbeat -- polling
# subscribed resources for recovery purposes is endorsed. Our heartbeat
# runs continuously rather than only "following perceived loss" because
# we can't reliably DETECT a loss when notifications fail silently --
# the heartbeat IS the detection mechanism. That's a documented
# deviation from a strict reading of (g) but consistent with its
# intent.
#
# Rule (r) is a documented deviation: we DO poll subscribed resources
# (at a heavily-reduced rate). Our spec doc 11-subscriptions.md
# already anticipates this trade-off: "consider reducing poll
# frequency for subscribed resources." The heartbeat formula honors
# that by clamping at a much longer cadence than the configured rate.
#
# Rule (f) is handled by SubscriptionManager.renew's on_subscription_lost
# callback -- a 401/404 on renewal triggers rediscovery.
#
# Formula: `max(_HEARTBEAT_CEILING_S, configured_interval)`.
#
# - `_HEARTBEAT_CEILING_S = 900` (15 min) is the spec's own threshold
#   for the non-subscriber case: §10.2.2.3 rule (a) p.101 says
#   "clients that do not subscribe to event lists SHALL poll at the
#   less frequent of every 15 minutes or pollRate." Borrowing that
#   threshold for the subscriber heartbeat gives subscribers the same
#   missed-event latency bound that non-subscribers are guaranteed.
# - The `max(..., configured_interval)` clamp guarantees the
#   heartbeat is NEVER more frequent than the configured rate. If an
#   operator sets a long interval (e.g. 1h dcap), the heartbeat
#   matches it rather than racing ahead.
#
# Concrete heartbeat cadences:
#   interval=30s  -> heartbeat every 900s   (30x slower than configured)
#   interval=300s -> heartbeat every 900s   (3x  slower than configured)
#   interval=900s -> heartbeat every 900s   (same as configured)
#   interval=3600s-> heartbeat every 3600s  (same as configured)
_HEARTBEAT_CEILING_S = 900.0


class PollScheduler:
    """Schedule periodic async callbacks with per-key mutual exclusion."""

    def __init__(self, *, heartbeat_enabled: bool = True) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._shutdown = asyncio.Event()
        self._last_skip_log: dict[str, float] = {}
        self._suppressed: set[str] = set()
        # Keys whose most recent callback raised. Cleared when the next
        # callback succeeds, at which point we emit a recovery info log so
        # the operator can see the failure was transient.
        self._failed_keys: set[str] = set()
        # Last monotonic timestamp a callback actually ran for each key.
        # Used by the heartbeat baseline poll to decide whether a
        # suppressed key is overdue for its safety-net run.
        self._last_run: dict[str, float] = {}
        # Whether the safety-net heartbeat for suppressed keys is on.
        # Default: enabled. Operators can disable via subscription config
        # if they want strict IEEE 2030.5 §8.9.3.4 rule (r) compliance
        # (no polling of subscribed resources at all) and accept the
        # missed-notification risk.
        self._heartbeat_enabled = heartbeat_enabled

    def schedule(
        self,
        key: str,
        interval: int,
        callback: Callable[[], Awaitable[None]],
    ) -> None:
        """Schedule a periodic poll. Replaces any existing poll for the same key."""
        if key in self._tasks:
            self._tasks[key].cancel()

        if key not in self._locks:
            self._locks[key] = asyncio.Lock()

        # Reset _last_run on every schedule (including re-schedule of an
        # existing key) so the first heartbeat check measures age from the
        # current schedule call, not from a stale prior run. Without this,
        # a key that was already suppressed at schedule time would
        # heartbeat-fire on its very first iteration (age=huge vs
        # threshold) when there's no prior entry; and a re-scheduled key
        # would use the *old* task's last run, producing either an
        # immediate fire or a delayed one based on how long ago that was.
        # Both cases violate the contract that the heartbeat clock starts
        # at schedule time.
        self._last_run[key] = time.monotonic()

        self._tasks[key] = asyncio.create_task(
            self._poll_loop(key, interval, callback),
            name=f"poll-{key}",
        )

    def _heartbeat_due(self, key: str, interval: int) -> bool:
        """Return True if a suppressed key is overdue for its heartbeat poll.

        Heartbeat threshold is ``max(_HEARTBEAT_CEILING_S, interval)`` --
        never faster than the configured rate, never slower than the
        ceiling. See module-level comment for spec rationale.
        """
        if not self._heartbeat_enabled:
            return False
        last = self._last_run.get(key, 0.0)
        age = time.monotonic() - last
        threshold = max(_HEARTBEAT_CEILING_S, float(interval))
        return age >= threshold

    async def _poll_loop(
        self,
        key: str,
        interval: int,
        callback: Callable[[], Awaitable[None]],
    ) -> None:
        lock = self._locks[key]
        while not self._shutdown.is_set():
            suppressed = key in self._suppressed
            heartbeat = suppressed and self._heartbeat_due(key, interval)
            if suppressed and not heartbeat:
                logger.debug("Poll %s suppressed by active subscription", key)
            elif lock.locked():
                now = time.monotonic()
                last = self._last_skip_log.get(key, 0.0)
                if now - last >= _SKIP_LOG_THROTTLE:
                    logger.debug("Poll %s skipped: previous still running", key)
                    self._last_skip_log[key] = now
            else:
                if heartbeat:
                    logger.debug(
                        "Poll %s heartbeat firing (suppressed by subscription, "
                        "but baseline safety net for missed notifications)",
                        key,
                    )
                async with lock:
                    self._last_run[key] = time.monotonic()
                    try:
                        await callback()
                    except Sep2ConnectionError as exc:
                        # Expected transient — server restart, network blip.
                        # Log concisely; next tick will retry.
                        self._failed_keys.add(key)
                        from py20305.diagnostics import report

                        report(
                            "warnings",
                            f"Poll {key}: server unreachable ({exc})",
                            source="polling",
                            dedup_key=f"poll:{key}:connection",
                            details={"poll_key": key, "kind": "connection", "error": str(exc)},
                        )
                    except Sep2ProtocolError as exc:
                        # Server responded with a non-success status. Not a
                        # bug — just a protocol-level outcome; the poll
                        # callback will see the same status next tick if
                        # it persists.
                        self._failed_keys.add(key)
                        from py20305.diagnostics import report

                        report(
                            "warnings",
                            f"Poll {key}: server returned HTTP {exc.status_code}",
                            source="polling",
                            dedup_key=f"poll:{key}:{exc.status_code}",
                            details={
                                "poll_key": key,
                                "kind": "protocol",
                                "status_code": exc.status_code,
                            },
                        )
                    except Exception as exc:
                        self._failed_keys.add(key)
                        from py20305.diagnostics import report

                        report(
                            "warnings",
                            f"Poll {key} callback failed: {exc}",
                            source="polling",
                            dedup_key=f"poll:{key}:{type(exc).__name__}",
                            details={
                                "poll_key": key,
                                "kind": "exception",
                                "exc_kind": type(exc).__name__,
                                "error": str(exc),
                            },
                            exc_info=True,
                        )
                    else:
                        # Success after a logged failure — close the loop in
                        # the operator's log so they can see we recovered,
                        # not just that we previously failed.
                        if key in self._failed_keys:
                            self._failed_keys.discard(key)
                            logger.info("Poll %s: recovered", key)

            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=float(interval))
                # shutdown was set
                return
            except TimeoutError:
                pass

    async def cancel_all(self, timeout: float = 10.0) -> None:
        """Signal shutdown and wait for all poll tasks to finish."""
        self._shutdown.set()
        tasks = list(self._tasks.values())
        if not tasks:
            return

        for task in tasks:
            task.cancel()

        _done, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            logger.warning("%d poll tasks did not finish within %.1fs", len(pending), timeout)

        self._tasks.clear()

    def suppress(self, key: str) -> None:
        """Suppress scheduled polls for *key* (subscription covers it)."""
        self._suppressed.add(key)

    def unsuppress(self, key: str) -> None:
        """Resume scheduled polls for *key*."""
        self._suppressed.discard(key)

    @property
    def suppressed_keys(self) -> set[str]:
        """Return the set of currently suppressed poll keys."""
        return set(self._suppressed)

    @property
    def active_keys(self) -> list[str]:
        """Return keys of currently scheduled (non-done) polls."""
        return [k for k, t in self._tasks.items() if not t.done()]
