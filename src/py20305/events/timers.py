"""Async event timer scheduling for activation and completion.

Each timer is an asyncio.Task with adaptive sleep: coarse (1s) when far
from target, fine (20ms) when near. Timers are shutdown-aware via an
asyncio.Event and check EventRecord.state before firing callbacks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from py20305.events.state_machine import EventRecord, EventState

logger = logging.getLogger(__name__)

# Timer fires this many seconds before the target time.
# The original threading implementation needed 10s/5s leads for thread
# coordination overhead. Our asyncio adaptive timer is precise to ~20ms,
# so no lead is needed.
ACTIVATION_LEAD = 0
COMPLETION_LEAD = 0


def _local_now() -> int:
    """Default timer clock: local wall time."""
    return int(time.time())


class EventTimerManager:
    """Manages asyncio.Task timers for event activation and completion."""

    def __init__(self, shutdown: asyncio.Event) -> None:
        self._shutdown = shutdown
        self._tasks: dict[bytes, list[asyncio.Task[None]]] = {}

    def schedule_activation(
        self,
        record: EventRecord,
        callback: Callable[[EventRecord], Awaitable[None]],
        *,
        now_fn: Callable[[], int] | None = None,
    ) -> None:
        """Schedule activation callback to fire at the event start time.

        ``now_fn`` supplies the clock the timer fires against (default local
        wall clock); the event processor passes a server-timebase clock so
        server-epoch targets fire on server time.
        """
        now_fn = now_fn or _local_now
        target = record.start - ACTIVATION_LEAD
        delay = target - now_fn()
        logger.debug(
            "Scheduling activation timer for %s in %ds",
            record.mrid.hex()[:8],
            max(delay, 0),
        )
        task = asyncio.create_task(
            self._wait_and_fire(record, target, callback, "activation", now_fn),
            name=f"activation-{record.mrid.hex()[:8]}",
        )
        self._tasks.setdefault(record.mrid, []).append(task)

    def schedule_completion(
        self,
        record: EventRecord,
        callback: Callable[[EventRecord], Awaitable[None]],
        *,
        now_fn: Callable[[], int] | None = None,
    ) -> None:
        """Schedule completion callback to fire at the event end time.

        See ``schedule_activation`` for ``now_fn``.
        """
        now_fn = now_fn or _local_now
        target = record.end - COMPLETION_LEAD
        delay = target - now_fn()
        logger.debug(
            "Scheduling completion timer for %s in %ds",
            record.mrid.hex()[:8],
            max(delay, 0),
        )
        task = asyncio.create_task(
            self._wait_and_fire(record, target, callback, "completion", now_fn),
            name=f"completion-{record.mrid.hex()[:8]}",
        )
        self._tasks.setdefault(record.mrid, []).append(task)

    def schedule_delayed_callback(
        self,
        record: EventRecord,
        delay_seconds: int,
        callback: Callable[[EventRecord], Awaitable[None]],
        label: str = "delayed",
    ) -> None:
        """Schedule a callback to fire after a fixed delay from now."""
        target = int(time.time()) + delay_seconds
        logger.debug(
            "Scheduling %s callback for %s in %ds",
            label,
            record.mrid.hex()[:8],
            delay_seconds,
        )
        task = asyncio.create_task(
            # Delayed callbacks are elapsed-time semantics (target anchored to
            # local now above), so they deliberately stay on the local clock.
            self._wait_and_fire(record, target, callback, label, _local_now),
            name=f"{label}-{record.mrid.hex()[:8]}",
        )
        self._tasks.setdefault(record.mrid, []).append(task)

    def has_pending(self, mrid: bytes) -> bool:
        """Return True if any timers are pending for the given mRID."""
        return mrid in self._tasks

    def cancel(self, mrid: bytes) -> None:
        """Cancel all timers for a given mRID.

        Safe to call from within a timer callback: the currently running task
        is untracked but never cancelled, so the callback that requested the
        cancellation (e.g. the comms-loss opt-out gate inside ``_on_activation``)
        finishes its own work instead of being killed at its next await.
        """
        tasks = self._tasks.pop(mrid, [])
        current = asyncio.current_task()
        for task in tasks:
            if task is not current:
                task.cancel()

    async def cancel_all(self) -> None:
        """Cancel all outstanding timers and wait for them to finish."""
        all_tasks: list[asyncio.Task[None]] = []
        for tasks in self._tasks.values():
            all_tasks.extend(tasks)
        self._tasks.clear()
        for task in all_tasks:
            task.cancel()
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)

    async def _wait_and_fire(
        self,
        record: EventRecord,
        target: int,
        callback: Callable[[EventRecord], Awaitable[None]],
        label: str,
        now_fn: Callable[[], int],
    ) -> None:
        """Adaptive sleep until target time, then fire callback if still valid.

        ``remaining`` is recomputed against ``now_fn`` on every tick, so a
        server-timebase offset update mid-sleep re-aims the firing instant
        without rescheduling.
        """
        try:
            while not self._shutdown.is_set():
                remaining = target - now_fn()
                if remaining <= 0:
                    break
                # Adaptive sleep: coarse when far, fine when near
                if remaining > 10:
                    sleep_time = 1.0
                elif remaining > 1:
                    sleep_time = 0.1
                else:
                    sleep_time = 0.02

                try:
                    await asyncio.wait_for(
                        self._shutdown_waiter(),
                        timeout=sleep_time,
                    )
                    # Shutdown was signaled
                    return
                except TimeoutError:
                    continue

            if self._shutdown.is_set():
                return

            # Check state before firing -- skip if cancelled/superseded
            if record.state in (EventState.CANCELLED, EventState.SUPERSEDED, EventState.COMPLETED):
                logger.debug(
                    "Skipping %s timer for %s: state=%s",
                    label,
                    record.mrid.hex()[:8],
                    record.state.value,
                )
                return

            logger.debug("Firing %s timer for %s", label, record.mrid.hex()[:8])
            await callback(record)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning(
                "Error in %s timer for %s",
                label,
                record.mrid.hex()[:8],
                exc_info=True,
            )

    async def _shutdown_waiter(self) -> None:
        """Wait for shutdown event. Used as a cancellable waiter."""
        await self._shutdown.wait()
