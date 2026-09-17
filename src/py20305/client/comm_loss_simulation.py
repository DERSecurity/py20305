"""Operator-triggered simulation of upstream communications loss.

An operator validating loss-of-communications behavior against a production
head-end cannot take that server out of service, and firewalling the device by
hand is not repeatable across a fleet of containers, native installs and
constrained edge devices. This module supplies the missing input: it stops
outbound requests from reaching the server so the ordinary detector in
``CsipClient`` sees the silence and reacts exactly as it would in a real outage.

The gate deliberately sits in the session object rather than in
``Sep2Client._get_session()`` itself. Every request path calls ``_get_session()``
*before* entering its own ``try``, so a failure raised there would escape each
path's error accounting: typed GET records connectivity inline in its own
``except`` ladder, ``get_raw``/``request_raw`` convert transport errors to their
documented ``{"status_code": 0, ...}`` return, and only non-GET traffic funnels
through ``_send_tracked``. Raising from ``async with session.get(...)`` instead
puts the failure inside every caller's ``try``, so all of that existing handling
runs unchanged and the simulated failure is indistinguishable from a real one.

``ClientOSError`` is the exception for the same reason: it is both an
``aiohttp.ClientError`` and an ``OSError``, so it satisfies every handler in the
client -- including ``with_retry``, which classifies ``OSError`` as a transient
transport failure and wraps it into ``Sep2ConnectionError``.

Request tracking runs whether or not the gate is armed. That is what lets
activation wait for in-flight requests to finish: one already awaiting aiohttp
when the window opens would otherwise complete normally and refresh
``last_contact_epoch`` *after* the operator was told the link was down.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import aiohttp

#: Message carried by the simulated transport failure. Deliberately explicit:
#: it surfaces in ``last_error`` and in diagnostics, and an operator reading a
#: captured log must never mistake it for a genuine outage.
SIMULATED_FAILURE_MESSAGE = "simulated loss of communications (operator-triggered)"

#: Phases reported to the management API. ``recovering`` and the two terminal
#: phases exist because clearing the gate is not the same event as leaving
#: comms-loss mode -- recovery re-polls schedules and can still fail.
PHASE_INACTIVE = "inactive"
PHASE_ACTIVE = "active"
PHASE_RECOVERING = "recovering"
PHASE_RECOVERED = "recovered"
PHASE_RECOVERY_FAILED = "recovery_failed"


class CommLossSimulation:
    """Arm state for a simulated outage, and the in-flight request count.

    One instance per :class:`~py20305.client.http.Sep2Client`. The client owns
    the mechanism; policy (whether simulation is permitted at all, and how long
    a window may run) belongs to the application driving it.
    """

    def __init__(self) -> None:
        self._active = False
        self._started: float | None = None
        self._expires: float | None = None
        self._phase = PHASE_INACTIVE
        self._detail: str | None = None
        self._in_flight = 0
        # Starts set: with nothing in flight, a drain request returns at once.
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def active(self) -> bool:
        """Whether outbound requests are currently being failed."""
        return self._active

    @property
    def expires_at(self) -> float | None:
        """Epoch seconds at which the window self-clears, or ``None``."""
        return self._expires

    @property
    def in_flight(self) -> int:
        """Requests currently between ``__aenter__`` and ``__aexit__``."""
        return self._in_flight

    def arm(self, *, expires_at: float) -> None:
        """Begin failing outbound requests."""
        self._active = True
        self._started = time.time()
        self._expires = expires_at
        self._phase = PHASE_ACTIVE
        self._detail = None

    def disarm(self) -> None:
        """Stop failing outbound requests, and enter the recovering phase.

        The phase does not go straight to ``recovered``: the caller still has to
        drive recovery, which re-polls schedules and may fail.
        """
        self._active = False
        self._expires = None
        self._phase = PHASE_RECOVERING
        self._detail = None

    def note_recovered(self) -> None:
        """Mark recovery complete and the window fully closed."""
        self._phase = PHASE_RECOVERED
        self._started = None
        self._detail = None

    def note_recovery_failed(self, detail: str) -> None:
        """Mark recovery as having failed, keeping the reason for the operator."""
        self._phase = PHASE_RECOVERY_FAILED
        self._detail = detail

    def note_request_started(self) -> None:
        """Count a request that has begun talking to the server."""
        self._in_flight += 1
        self._idle.clear()

    def note_request_finished(self) -> None:
        """Release a counted request, waking any drain waiter at zero."""
        self._in_flight -= 1
        if self._in_flight <= 0:
            self._in_flight = 0
            self._idle.set()

    async def wait_until_idle(self, timeout: float) -> bool:
        """Wait for in-flight requests to finish. True if they did, in time.

        A False return is the caller's signal that draining did not complete and
        the remaining requests have to be dealt with another way -- the window
        must not be reported active while a request could still refresh
        ``last_contact_epoch``.
        """
        try:
            await asyncio.wait_for(self._idle.wait(), timeout)
        except TimeoutError:
            return False
        return True

    def status(self) -> dict[str, Any]:
        """Serialize for the management API's simulation block."""
        return {
            "active": self._active,
            "phase": self._phase,
            "started": self._started,
            "expires": self._expires,
            "detail": self._detail,
        }


class _GatedRequestContext:
    """Async context manager standing in for aiohttp's request context.

    Must not be a coroutine: every call site uses ``async with session.get(...)``
    rather than awaiting it, so an ``async def`` request method would hand back a
    coroutine, and the ``async with`` would fail with ``TypeError`` -- an error
    raised in the wrong place, outside the transport-error handling this design
    depends on.

    The real context manager is built lazily, inside ``__aenter__``, so an armed
    gate never constructs one. Building it eagerly would leave an un-awaited
    request coroutine behind on every gated call.
    """

    def __init__(self, simulation: CommLossSimulation, factory: Callable[[], Any]) -> None:
        self._simulation = simulation
        self._factory = factory
        self._inner: Any = None
        self._counted = False

    async def __aenter__(self) -> Any:
        if self._simulation.active:
            raise aiohttp.ClientOSError(SIMULATED_FAILURE_MESSAGE)
        self._simulation.note_request_started()
        self._counted = True
        try:
            self._inner = self._factory()
            return await self._inner.__aenter__()
        except BaseException:
            # A real failure in the underlying request never reaches __aexit__,
            # so the count has to be released here or a drain would hang.
            self._release()
            raise

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        try:
            if self._inner is not None:
                return await self._inner.__aexit__(exc_type, exc, tb)
            return None
        finally:
            self._release()

    def _release(self) -> None:
        if self._counted:
            self._counted = False
            self._simulation.note_request_finished()


class GatedSession:
    """``aiohttp.ClientSession`` wrapper that can fail every request on demand.

    Wraps the session on every call, armed or not, so request tracking is
    continuous and one code path serves both states. Only the request methods
    are wrapped; ``close()`` and friends are reached through the underlying
    session directly by the client that owns it.
    """

    def __init__(self, session: aiohttp.ClientSession, simulation: CommLossSimulation) -> None:
        self._session = session
        self._simulation = simulation

    @property
    def session(self) -> aiohttp.ClientSession:
        """The wrapped session, for callers needing the real object."""
        return self._session

    def __getattr__(self, item: str) -> Any:
        """Delegate everything else to the wrapped session.

        The wrapper intercepts requests and nothing else, so ``headers``,
        ``closed``, ``close()`` and the rest stay reachable and behave as they
        did before. Only ``__getattr__`` misses reach here, so the request
        methods defined above always win.
        """
        return getattr(self._session, item)

    def get(self, *args: Any, **kwargs: Any) -> _GatedRequestContext:
        return _GatedRequestContext(self._simulation, lambda: self._session.get(*args, **kwargs))

    def post(self, *args: Any, **kwargs: Any) -> _GatedRequestContext:
        return _GatedRequestContext(self._simulation, lambda: self._session.post(*args, **kwargs))

    def put(self, *args: Any, **kwargs: Any) -> _GatedRequestContext:
        return _GatedRequestContext(self._simulation, lambda: self._session.put(*args, **kwargs))

    def delete(self, *args: Any, **kwargs: Any) -> _GatedRequestContext:
        return _GatedRequestContext(self._simulation, lambda: self._session.delete(*args, **kwargs))

    def request(self, *args: Any, **kwargs: Any) -> _GatedRequestContext:
        return _GatedRequestContext(
            self._simulation, lambda: self._session.request(*args, **kwargs)
        )
