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


class LimiterFailure(Exception):  # noqa: N818 -- a carrier, not an error the caller sees
    """Carry an exception raised by :meth:`RequestLimiter.acquire` past the transport handling.

    The HTTP layer retries transport failures and counts them against the
    server's health. A limiter that fails has sent nothing to the server, so
    its exception is wrapped in this class, which no transport handler
    catches, and unwrapped where the request returns to its caller.
    """

    def __init__(self, error: Exception) -> None:
        super().__init__(str(error))
        #: The exception ``acquire`` raised.
        self.error = error


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
