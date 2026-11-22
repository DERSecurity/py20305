"""A hook that lets several processes share one client's request budget.

A server can limit how often one client sends requests. When a host
application runs several processes as one client, they share that limit. The
host passes each process's client a :class:`RequestLimiter`, and the HTTP layer
awaits :meth:`RequestLimiter.acquire` before each request it sends to the
server, every retry attempt included. A limiter backed by shared storage (a
token bucket in a database, for example) then paces all the processes together.

No limiter is the default: requests go out as soon as they are made.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["RequestLimiter"]


@runtime_checkable
class RequestLimiter(Protocol):
    """Decides when the next request to the server may be sent."""

    async def acquire(self) -> None:
        """Wait until one more request may be sent, then return.

        Called once per request, before it is sent. An exception raised here
        stops the request and reaches the code that made it unchanged, so a
        limiter should wait rather than raise when the budget is spent.
        """
        ...
